"""Fixed-location ADR-009 registry retrieval and durable accepted-state cache."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Awaitable, Callable

import aiohttp

from ..signed_registry import (
    AcceptedRegistryState,
    RegistryEnvelope,
    RegistryErrorCode,
    RegistryValidationError,
    ReleaseRevocation,
    ReviewedReleaseEntry,
    TrustAnchorStore,
    ValidationStatus,
    canonical_json,
    parse_verified_registry_envelope,
    validate_registry_envelope,
)
from ..signed_registry.models import MAX_ENVELOPE_BYTES, parse_utc_timestamp
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
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_REVOCATION_SOURCE_ENVELOPES = 8
MAX_FAILURE_REASONS = 32


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
        self._revocation_sources: tuple[RegistryEnvelope, ...] = ()
        self._last_refresh_monotonic: float | None = None
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

    async def refresh(self) -> bool:
        if not self._enabled:
            return False
        async with self._lock:
            self._last_refresh_monotonic = time.monotonic()
            self._last_refresh_status = "refreshing"
            try:
                raw = await self._fetcher(REGISTRY_URL, MAX_ENVELOPE_BYTES)
                accepted_state = (
                    AcceptedRegistryState.from_envelope(self._accepted)
                    if self._accepted is not None
                    else None
                )
                result = validate_registry_envelope(
                    raw,
                    trust_anchors=self._anchors,
                    now=self._now(),
                    accepted_state=accepted_state,
                )
                if not result.accepted or result.envelope is None:
                    code = (
                        result.issues[0].code.value
                        if len(result.issues) == 1
                        else "registry_validation_failed"
                    )
                    raise ReleaseRegistryOperationalError(code)
                if result.envelope.registry_id != REGISTRY_ID:
                    raise ReleaseRegistryOperationalError(
                        RegistryErrorCode.REGISTRY_ID_MISMATCH.value
                    )
                if result.status is ValidationStatus.IDEMPOTENT_REPLAY:
                    self._last_refresh_status = "idempotent"
                    self._last_failure_reason = None
                    return True
                sources = self._next_revocation_sources(result.envelope)
                self._write_cache(result.envelope, sources)
                self._accepted = result.envelope
                self._revocation_sources = sources
                self._cache_status = "valid"
                self._last_refresh_status = "accepted"
                self._last_failure_reason = None
                return True
            except ReleaseRegistryOperationalError as exc:
                self._record_failure(exc.reason_code)
                return False
            except RegistryValidationError as exc:
                self._record_failure(exc.code.value)
                return False
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                self._record_failure("registry_unavailable")
                return False
            except Exception:
                self._record_failure("registry_invalid")
                return False

    def authority(self) -> ReleaseRegistryAuthority:
        envelope = self._accepted
        current = bool(envelope and self._positive_is_current(envelope))
        revocations: dict[
            tuple[str, str, str, str], ReleaseRevocation
        ] = {}
        for source in self._revocation_sources:
            for item in source.revocations:
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
            sequence=envelope.sequence if envelope else None,
            content_digest=envelope.content_digest if envelope else None,
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
                if authority.revocations
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
                raw = await response.content.read(maximum + 1)
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

    def _next_revocation_sources(
        self,
        candidate: RegistryEnvelope,
    ) -> tuple[RegistryEnvelope, ...]:
        retained = {
            item.revocation_identity
            for source in self._revocation_sources
            for item in source.revocations
        }
        retained.update(
            item.revocation_identity
            for item in (
                self._accepted.revocations
                if self._accepted is not None
                else ()
            )
        )
        candidate_ids = {
            item.revocation_identity for item in candidate.revocations
        }
        if retained <= candidate_ids:
            return (candidate,) if candidate.revocations else ()
        sources = tuple(
            source
            for source in self._revocation_sources
            if source.content_digest != candidate.content_digest
        )
        if self._accepted is not None and self._accepted.revocations:
            sources += (self._accepted,)
        if candidate.revocations:
            sources += (candidate,)
        deduplicated = {
            source.content_digest: source for source in sources
        }
        result = tuple(
            deduplicated[key] for key in sorted(deduplicated)
        )
        if len(result) > MAX_REVOCATION_SOURCE_ENVELOPES:
            raise ReleaseRegistryOperationalError(
                "registry_revocation_history_capacity_exhausted"
            )
        return result

    def _load_cache(self) -> None:
        try:
            raw = self._cache_path.read_bytes()
            if len(raw) > MAX_CACHE_BYTES:
                raise ReleaseRegistryOperationalError(
                    "registry_cache_oversized"
                )
            value = _strict_cache_json(raw)
            if not isinstance(value, dict) or set(value) != {
                "schema_version",
                "accepted_registry",
                "revocation_sources",
            }:
                raise ReleaseRegistryOperationalError(
                    "registry_cache_invalid"
                )
            if value["schema_version"] != 1:
                raise ReleaseRegistryOperationalError(
                    "registry_cache_schema_unsupported"
                )
            accepted = parse_verified_registry_envelope(
                canonical_json(value["accepted_registry"]),
                trust_anchors=self._anchors,
            )
            self._require_cache_envelope(accepted)
            raw_sources = value["revocation_sources"]
            if (
                not isinstance(raw_sources, list)
                or len(raw_sources) > MAX_REVOCATION_SOURCE_ENVELOPES
            ):
                raise ReleaseRegistryOperationalError(
                    "registry_cache_invalid"
                )
            sources = tuple(
                parse_verified_registry_envelope(
                    canonical_json(item),
                    trust_anchors=self._anchors,
                )
                for item in raw_sources
            )
            for source in sources:
                self._require_cache_envelope(source)
                if (
                    source.sequence > accepted.sequence
                    or not source.revocations
                ):
                    raise ReleaseRegistryOperationalError(
                        "registry_cache_invalid"
                    )
            self._accepted = accepted
            self._revocation_sources = sources
            self._cache_status = (
                "valid"
                if self._positive_is_current(accepted)
                else "denial_only"
            )
        except FileNotFoundError:
            self._cache_status = "missing"
        except Exception:
            self._accepted = None
            self._revocation_sources = ()
            self._cache_status = "invalid"
            self._record_failure("registry_cache_invalid")

    def _require_cache_envelope(self, envelope: RegistryEnvelope) -> None:
        if (
            envelope.registry_id != REGISTRY_ID
            or envelope.key_id != TRUST_ANCHOR_KEY_ID
        ):
            raise ReleaseRegistryOperationalError(
                "registry_cache_invalid"
            )

    def _write_cache(
        self,
        accepted: RegistryEnvelope,
        sources: tuple[RegistryEnvelope, ...],
    ) -> None:
        encoded = canonical_json(
            {
                "schema_version": 1,
                "accepted_registry": accepted.to_mapping(),
                "revocation_sources": [
                    source.to_mapping() for source in sources
                ],
            }
        )
        if len(encoded) > MAX_CACHE_BYTES:
            raise ReleaseRegistryOperationalError(
                "registry_cache_oversized"
            )
        parent = self._cache_path.parent
        handle = None
        temporary: Path | None = None
        backup: Path | None = None
        replaced = False
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if self._cache_path.exists():
                previous = self._cache_path.read_bytes()
                if len(previous) > MAX_CACHE_BYTES:
                    raise ReleaseRegistryOperationalError(
                        "registry_cache_oversized"
                    )
                backup_handle = tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{self._cache_path.name}.backup.",
                    dir=parent,
                    delete=False,
                )
                backup = Path(backup_handle.name)
                with backup_handle:
                    backup_handle.write(previous)
                    backup_handle.flush()
                    os.fsync(backup_handle.fileno())
                self._fsync_directory(parent)
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
            replaced = True
            self._fsync_directory(parent)
        except OSError as exc:
            if replaced:
                try:
                    if backup is not None:
                        os.replace(backup, self._cache_path)
                        backup = None
                    else:
                        self._cache_path.unlink(missing_ok=True)
                    self._fsync_directory(parent)
                except OSError:
                    pass
            raise ReleaseRegistryOperationalError(
                "registry_cache_write_failed"
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if backup is not None:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass

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


def _strict_cache_json(raw: bytes) -> Any:
    def reject_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseRegistryOperationalError(
                    "registry_cache_invalid"
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ReleaseRegistryOperationalError("registry_cache_invalid")

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
            "registry_cache_invalid"
        ) from None
