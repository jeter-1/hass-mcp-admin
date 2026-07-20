"""Verified, bounded last-known-good registry for upstream release attestations."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Mapping

import aiohttp
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .upstream_contracts import (
    CONTRACT_FAMILY,
    ContractValidationError,
    ReleaseAttestation,
    _validate_unique_attestations,
    canonical_json,
    load_attestations,
)


REGISTRY_URL = (
    "https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/"
    "main/upstream-trust/upstream-dashboard-registry.json"
)
SIGNATURE_URL = (
    "https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/"
    "main/upstream-trust/upstream-dashboard-registry.sig.json"
)
CACHE_PATH = Path("/data/upstream-dashboard-trust-registry-cache.json")
MAX_REGISTRY_BYTES = 262_144
MAX_SIGNATURE_BYTES = 4_096
REFRESH_INTERVAL_SECONDS = 21_600.0
CACHE_HARD_AGE_SECONDS = 604_800.0
CONNECT_TIMEOUT_SECONDS = 5.0
TOTAL_TIMEOUT_SECONDS = 15.0
MAX_CLOCK_SKEW_SECONDS = 300.0


class RegistryValidationError(ValueError):
    """Stable registry rejection category without raw remote content."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class VerifiedRegistry:
    sequence: int
    generated_at: str
    expires_at: str
    key_id: str
    entries: tuple[ReleaseAttestation, ...]
    signature_valid: bool
    accepted_at: str
    source: str
    payload_digest: str


@dataclass
class RegistryHealth:
    enabled: bool = False
    sequence: int | None = None
    generated_at: str | None = None
    refresh_status: str = "disabled"
    last_successful_refresh: str | None = None
    last_failure_category: str | None = None
    signature_valid: bool | None = None
    cache_status: str = "not_loaded"
    cache_loaded_at: str | None = None


Fetcher = Callable[[str, int], Any]


class UpstreamTrustRegistry:
    """Load signed release data without allowing it to define runtime policy."""

    def __init__(
        self,
        *,
        enabled: bool,
        public_key: str,
        cache_path: Path = CACHE_PATH,
        fetcher: Fetcher | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._public_key = _parse_public_key(public_key) if enabled else None
        self._cache_path = cache_path
        self._fetcher = fetcher or self._fetch_bytes
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._builtins = load_attestations()
        self._remote: VerifiedRegistry | None = None
        self._lock = asyncio.Lock()
        self._last_refresh_monotonic: float | None = None
        self.health = RegistryHealth(enabled=enabled)
        if enabled:
            self.health.refresh_status = "not_attempted"
            self._load_cache()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def refresh_due(self) -> bool:
        if not self._enabled or self._last_refresh_monotonic is None:
            return self._enabled
        return time.monotonic() - self._last_refresh_monotonic >= REFRESH_INTERVAL_SECONDS

    @property
    def last_failure_category(self) -> str | None:
        return self.health.last_failure_category

    def has_exact_attestation(self, server_name: str, version: str) -> bool:
        return any(
            entry.server_name == server_name
            and entry.upstream_version == version
            and entry.contract_family == CONTRACT_FAMILY
            for entry, _source in self.effective_attestations()
        )

    def effective_attestations(self) -> tuple[tuple[ReleaseAttestation, str], ...]:
        keyed = {
            (entry.server_name, entry.upstream_version, entry.contract_family): (
                entry,
                "builtin",
            )
            for entry in self._builtins
        }
        if self._remote is not None and self._registry_is_usable(self._remote):
            source = "remote_fresh" if self.health.refresh_status == "success" else "remote_cached"
            for entry in self._remote.entries:
                keyed[(entry.server_name, entry.upstream_version, entry.contract_family)] = (
                    entry,
                    source,
                )
        return tuple(keyed[key] for key in sorted(keyed))

    async def refresh_if_due(self, *, force: bool = False) -> bool:
        if not self._enabled:
            return False
        if not force and not self.refresh_due():
            return False
        return await self.refresh()

    async def refresh(self) -> bool:
        if not self._enabled:
            return False
        async with self._lock:
            self.health.refresh_status = "refreshing"
            self._last_refresh_monotonic = time.monotonic()
            try:
                registry_raw, signature_raw = await asyncio.gather(
                    self._fetcher(REGISTRY_URL, MAX_REGISTRY_BYTES),
                    self._fetcher(SIGNATURE_URL, MAX_SIGNATURE_BYTES),
                )
                verified = verify_registry(
                    registry_raw,
                    signature_raw,
                    public_key=self._public_key,
                    now=self._now(),
                    source="remote_fresh",
                )
                if self._remote is not None:
                    if verified.sequence < self._remote.sequence:
                        raise RegistryValidationError("upstream_registry_rollback")
                    if (
                        verified.sequence == self._remote.sequence
                        and verified.payload_digest != self._remote.payload_digest
                    ):
                        raise RegistryValidationError("upstream_registry_replay_conflict")
                self._write_cache(registry_raw, signature_raw, verified)
                self._remote = verified
                self.health.sequence = verified.sequence
                self.health.generated_at = verified.generated_at
                self.health.refresh_status = "success"
                self.health.last_successful_refresh = _utc_now(self._now())
                self.health.last_failure_category = None
                self.health.signature_valid = True
                self.health.cache_status = "valid"
                self.health.cache_loaded_at = verified.accepted_at
                return True
            except RegistryValidationError as exc:
                self.health.refresh_status = "failed"
                self.health.last_failure_category = exc.category
                if exc.category == "upstream_registry_invalid_signature":
                    self.health.signature_valid = False
                return False
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                self.health.refresh_status = "failed"
                self.health.last_failure_category = "upstream_registry_unavailable"
                return False
            except Exception:
                self.health.refresh_status = "failed"
                self.health.last_failure_category = "upstream_registry_invalid"
                return False

    def snapshot(self) -> dict[str, Any]:
        registry = self._remote
        now = self._now()
        cache_age = (
            None
            if registry is None
            else max(0.0, (now - _parse_timestamp(registry.accepted_at)).total_seconds())
        )
        registry_age = (
            None
            if registry is None
            else max(0.0, (now - _parse_timestamp(registry.generated_at)).total_seconds())
        )
        return {
            "registry_enabled": self.health.enabled,
            "registry_sequence": self.health.sequence,
            "registry_generated_at": self.health.generated_at,
            "registry_age_seconds": (
                None if registry_age is None else round(registry_age, 3)
            ),
            "registry_refresh_status": self.health.refresh_status,
            "last_successful_registry_refresh": self.health.last_successful_refresh,
            "last_registry_failure_category": self.health.last_failure_category,
            "signature_valid": self.health.signature_valid,
            "cache_status": self.health.cache_status,
            "cache_age_seconds": None if cache_age is None else round(cache_age, 3),
            "registry_refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
            "registry_cache_hard_age_seconds": CACHE_HARD_AGE_SECONDS,
            "registry_location": "fixed_repository_https",
        }

    def admission_rejection(self) -> tuple[str, str]:
        """Translate a failed required refresh into bounded admission evidence."""

        category = self.health.last_failure_category or "upstream_registry_unavailable"
        if category == "upstream_registry_invalid_signature":
            return "rejected_signature_failure", category
        if category == "upstream_registry_expired":
            return "rejected_expired_attestation", category
        return "rejected_registry_unavailable", category

    async def _fetch_bytes(self, url: str, maximum: int) -> bytes:
        if url not in {REGISTRY_URL, SIGNATURE_URL} or not url.startswith("https://"):
            raise RegistryValidationError("upstream_registry_location_rejected")
        timeout = aiohttp.ClientTimeout(
            total=TOTAL_TIMEOUT_SECONDS,
            connect=CONNECT_TIMEOUT_SECONDS,
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise RegistryValidationError("upstream_registry_unavailable")
                declared = response.content_length
                if declared is not None and declared > maximum:
                    raise RegistryValidationError("upstream_registry_oversized")
                result = await response.content.read(maximum + 1)
                if len(result) > maximum:
                    raise RegistryValidationError("upstream_registry_oversized")
                return result

    def _registry_is_usable(self, registry: VerifiedRegistry) -> bool:
        now = self._now()
        accepted = _parse_timestamp(registry.accepted_at)
        expires = _parse_timestamp(registry.expires_at)
        usable = now <= expires and (now - accepted).total_seconds() <= CACHE_HARD_AGE_SECONDS
        if not usable and self.health.cache_status == "valid":
            self.health.cache_status = "expired"
        return usable

    def _load_cache(self) -> None:
        try:
            raw = self._cache_path.read_bytes()
            if len(raw) > MAX_REGISTRY_BYTES + MAX_SIGNATURE_BYTES + 16_384:
                raise RegistryValidationError("upstream_registry_oversized")
            envelope = _strict_json_loads(raw)
            if not isinstance(envelope, dict) or set(envelope) != {
                "registry",
                "signature",
                "cached_at",
            }:
                raise RegistryValidationError("upstream_registry_cache_invalid")
            registry_raw = canonical_json(envelope["registry"])
            signature_raw = canonical_json(envelope["signature"])
            verified = verify_registry(
                registry_raw,
                signature_raw,
                public_key=self._public_key,
                now=self._now(),
                source="remote_cached",
                accepted_at=envelope["cached_at"],
            )
            if not self._registry_is_usable(verified):
                raise RegistryValidationError("upstream_registry_expired")
            self._remote = verified
            self.health.sequence = verified.sequence
            self.health.generated_at = verified.generated_at
            self.health.signature_valid = True
            self.health.cache_status = "valid"
            self.health.cache_loaded_at = verified.accepted_at
        except FileNotFoundError:
            self.health.cache_status = "missing"
        except Exception:
            self.health.cache_status = "invalid"
            self.health.last_failure_category = "upstream_registry_cache_invalid"

    def _write_cache(
        self,
        registry_raw: bytes,
        signature_raw: bytes,
        verified: VerifiedRegistry,
    ) -> None:
        envelope = {
            "registry": _strict_json_loads(registry_raw),
            "signature": _strict_json_loads(signature_raw),
            "cached_at": verified.accepted_at,
        }
        encoded = canonical_json(envelope)
        parent = self._cache_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{self._cache_path.name}.",
            dir=parent,
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._cache_path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def verify_registry(
    registry_raw: bytes,
    signature_raw: bytes,
    *,
    public_key: Ed25519PublicKey | None,
    now: datetime,
    source: str,
    accepted_at: str | None = None,
) -> VerifiedRegistry:
    if public_key is None:
        raise RegistryValidationError("upstream_registry_public_key_missing")
    if len(registry_raw) > MAX_REGISTRY_BYTES or len(signature_raw) > MAX_SIGNATURE_BYTES:
        raise RegistryValidationError("upstream_registry_oversized")
    registry = _strict_json_loads(registry_raw)
    signature = _strict_json_loads(signature_raw)
    if not isinstance(registry, dict) or set(registry) != {
        "schema_version",
        "sequence",
        "generated_at",
        "expires_at",
        "key_id",
        "entries",
    }:
        raise RegistryValidationError("upstream_registry_malformed")
    if not isinstance(signature, dict) or set(signature) != {
        "schema_version",
        "algorithm",
        "key_id",
        "signature",
    }:
        raise RegistryValidationError("upstream_registry_signature_malformed")
    if registry["schema_version"] != 1 or signature["schema_version"] != 1:
        raise RegistryValidationError("upstream_registry_schema_unsupported")
    if signature["algorithm"] != "Ed25519" or signature["key_id"] != registry["key_id"]:
        raise RegistryValidationError("upstream_registry_invalid_signature")
    try:
        signature_bytes = base64.b64decode(signature["signature"], validate=True)
        public_key.verify(signature_bytes, canonical_json(registry))
    except (binascii.Error, InvalidSignature, TypeError, ValueError):
        raise RegistryValidationError("upstream_registry_invalid_signature") from None
    sequence = registry["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise RegistryValidationError("upstream_registry_sequence_invalid")
    generated = _parse_timestamp(registry["generated_at"])
    expires = _parse_timestamp(registry["expires_at"])
    if generated.timestamp() - now.timestamp() > MAX_CLOCK_SKEW_SECONDS or expires <= generated:
        raise RegistryValidationError("upstream_registry_expired")
    if now > expires:
        raise RegistryValidationError("upstream_registry_expired")
    entries_value = registry["entries"]
    if not isinstance(entries_value, list) or len(entries_value) > 512:
        raise RegistryValidationError("upstream_registry_malformed")
    try:
        entries = tuple(ReleaseAttestation.from_mapping(item) for item in entries_value)
        _validate_unique_attestations(entries)
    except (ContractValidationError, TypeError):
        raise RegistryValidationError("upstream_registry_malformed") from None
    key_id = registry["key_id"]
    if not isinstance(key_id, str) or not re_full_key_id(key_id):
        raise RegistryValidationError("upstream_registry_key_id_invalid")
    return VerifiedRegistry(
        sequence=sequence,
        generated_at=registry["generated_at"],
        expires_at=registry["expires_at"],
        key_id=key_id,
        entries=entries,
        signature_valid=True,
        accepted_at=accepted_at or _utc_now(now),
        source=source,
        payload_digest=hashlib.sha256(canonical_json(registry)).hexdigest(),
    )


def _strict_json_loads(raw: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RegistryValidationError("upstream_registry_duplicate_key")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except RegistryValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RegistryValidationError("upstream_registry_malformed") from None


def _parse_public_key(value: str) -> Ed25519PublicKey:
    candidate = (value or "").strip()
    if candidate.startswith("ed25519:"):
        candidate = candidate.split(":", 1)[1]
    try:
        raw = base64.b64decode(candidate, validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PublicKey.from_public_bytes(raw)
    except (binascii.Error, ValueError):
        raise RegistryValidationError("upstream_registry_public_key_invalid") from None


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 32:
        raise RegistryValidationError("upstream_registry_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RegistryValidationError("upstream_registry_timestamp_invalid") from None
    if parsed.tzinfo is None:
        raise RegistryValidationError("upstream_registry_timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def re_full_key_id(value: str) -> bool:
    return bool(value and len(value) <= 80 and all(char.isalnum() or char in "._-" for char in value))


def _utc_now(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
