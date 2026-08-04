"""Immutable, versioned storage for exact dashboard-write planning artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import threading
from typing import Any

from .constants import (
    ARTIFACT_RETENTION_DAYS,
    ARTIFACT_SCHEMA,
    CANONICAL_PLAN_ID,
    MAX_ARTIFACT_BYTES,
)
from .errors import ArtifactStorageError
from .json_codec import canonical_json_bytes, engineering_sha256
from .models import DashboardArtifactRecord, DashboardUpdateProposal
from .serialization import private_proposal_projection, proposal_hash


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ArtifactStorageError("Artifact timestamp is malformed")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ArtifactStorageError("Artifact timestamp is malformed") from exc
    if parsed.tzinfo is None:
        raise ArtifactStorageError("Artifact timestamp is missing a timezone")
    return parsed.astimezone(timezone.utc)


class DashboardArtifactStore:
    """Caller-configured private namespace; no public arbitrary-path interface."""

    def __init__(
        self,
        root: str | Path,
        *,
        retention_days: int = ARTIFACT_RETENTION_DAYS,
    ) -> None:
        if not isinstance(retention_days, int) or not 1 <= retention_days <= 365:
            raise ArtifactStorageError("Artifact retention is outside the reviewed bound")
        candidate = Path(root)
        candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = candidate.resolve(strict=True)
        if not self.root.is_dir():
            raise ArtifactStorageError("Artifact root is not a directory")
        self.retention_days = retention_days
        self._lock = threading.RLock()

    def _path(self, plan_id: str) -> Path:
        if not isinstance(plan_id, str) or not CANONICAL_PLAN_ID.fullmatch(plan_id):
            raise ArtifactStorageError("Artifact plan ID is not canonical")
        return self.root / f"{plan_id}.json"

    def create(self, proposal: DashboardUpdateProposal) -> DashboardArtifactRecord:
        if proposal.proposal_sha256 != proposal_hash(proposal):
            raise ArtifactStorageError("Proposal hash binding is invalid")
        payload = private_proposal_projection(proposal)
        payload_sha256 = engineering_sha256(payload)
        record = DashboardArtifactRecord(
            schema=ARTIFACT_SCHEMA,
            plan_id=proposal.plan_id,
            created_at=proposal.created_at,
            expires_at=proposal.expires_at,
            proposal_sha256=proposal.proposal_sha256,
            payload_sha256=payload_sha256,
            payload=payload,
        )
        envelope = {
            "schema": record.schema,
            "plan_id": record.plan_id,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "proposal_sha256": record.proposal_sha256,
            "payload_sha256": record.payload_sha256,
            "payload": record.payload,
        }
        encoded = canonical_json_bytes(envelope)
        if len(encoded) > MAX_ARTIFACT_BYTES:
            raise ArtifactStorageError("Dashboard artifact exceeds the reviewed byte bound")

        destination = self._path(proposal.plan_id)
        temporary = self.root / (
            f".{proposal.plan_id}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        with self._lock:
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                try:
                    with os.fdopen(descriptor, "wb", closefd=True) as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.link(temporary, destination)
                    directory_fd = os.open(self.root, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    temporary.unlink(missing_ok=True)
            except FileExistsError as exc:
                temporary.unlink(missing_ok=True)
                raise ArtifactStorageError("Dashboard artifact is immutable and already exists") from exc
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise ArtifactStorageError("Atomic dashboard artifact write failed") from exc
        persisted = self.get(proposal.plan_id)
        if persisted is None or persisted != record:
            raise ArtifactStorageError("Dashboard artifact durable readback drifted")
        return persisted

    def get(self, plan_id: str) -> DashboardArtifactRecord | None:
        path = self._path(plan_id)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ArtifactStorageError("Dashboard artifact cannot be opened safely") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactStorageError("Dashboard artifact is not a regular file")
            if metadata.st_size > MAX_ARTIFACT_BYTES:
                raise ArtifactStorageError("Dashboard artifact exceeds the read bound")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                raw = handle.read(MAX_ARTIFACT_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ArtifactStorageError("Dashboard artifact exceeds the read bound")
        try:
            envelope = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactStorageError("Dashboard artifact is corrupt") from exc
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema",
            "plan_id",
            "created_at",
            "expires_at",
            "proposal_sha256",
            "payload_sha256",
            "payload",
        }:
            raise ArtifactStorageError("Dashboard artifact envelope is invalid")
        if envelope["schema"] != ARTIFACT_SCHEMA:
            raise ArtifactStorageError("Unknown dashboard artifact schema")
        if envelope["plan_id"] != plan_id:
            raise ArtifactStorageError("Dashboard artifact plan binding is invalid")
        if not isinstance(envelope["payload"], dict):
            raise ArtifactStorageError("Dashboard artifact payload is invalid")
        if engineering_sha256(envelope["payload"]) != envelope["payload_sha256"]:
            raise ArtifactStorageError("Dashboard artifact payload hash mismatch")
        if envelope["payload"].get("proposal_sha256") != envelope["proposal_sha256"]:
            raise ArtifactStorageError("Dashboard artifact proposal hash mismatch")
        proposal_projection = dict(envelope["payload"])
        proposal_projection.pop("proposal_sha256", None)
        if engineering_sha256(proposal_projection) != envelope["proposal_sha256"]:
            raise ArtifactStorageError("Dashboard artifact proposal binding is corrupt")
        _parse_timestamp(envelope["created_at"])
        _parse_timestamp(envelope["expires_at"])
        return DashboardArtifactRecord(
            schema=envelope["schema"],
            plan_id=plan_id,
            created_at=envelope["created_at"],
            expires_at=envelope["expires_at"],
            proposal_sha256=envelope["proposal_sha256"],
            payload_sha256=envelope["payload_sha256"],
            payload=envelope["payload"],
        )

    def prune_expired(self, *, now: datetime) -> int:
        """Remove only artifacts expired beyond the configured retention period."""

        if now.tzinfo is None:
            raise ArtifactStorageError("Retention time must include a timezone")
        cutoff = now.astimezone(timezone.utc) - timedelta(days=self.retention_days)
        removed = 0
        with self._lock:
            for path in sorted(self.root.glob("*.json")):
                if path.is_symlink():
                    continue
                try:
                    record = self.get(path.stem)
                except ArtifactStorageError:
                    continue
                if record is not None and _parse_timestamp(record.expires_at) < cutoff:
                    path.unlink()
                    removed += 1
        return removed


def artifact_resulting_configuration(record: DashboardArtifactRecord) -> dict[str, Any]:
    try:
        result = record.payload["compilation"]["resulting_configuration"]
    except (KeyError, TypeError) as exc:
        raise ArtifactStorageError("Dashboard artifact lacks the approved result") from exc
    if not isinstance(result, dict):
        raise ArtifactStorageError("Dashboard artifact result is malformed")
    expected = record.payload["compilation"].get("resulting_sha256")
    if engineering_sha256(result) != expected:
        raise ArtifactStorageError("Dashboard artifact result hash mismatch")
    return result
