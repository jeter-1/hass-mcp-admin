"""Zero-dispatch planning for one governed existing-dashboard update."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re
import secrets
from typing import Any, Iterable, Mapping, Protocol

from .artifact_store import DashboardArtifactStore
from .atomicity import assess_atomicity
from .constants import (
    CANONICAL_PLAN_ID,
    MAX_DESCRIPTION_CHARS,
    MAX_EXPIRATION_MINUTES,
    MAX_TITLE_CHARS,
    MIN_EXPIRATION_MINUTES,
    PROPOSAL_MODEL,
)
from .errors import (
    PatchCompilationError,
    PatchValidationError,
    PlanningError,
    ProviderAdmissionError,
    RawEvidenceError,
)
from .models import (
    DashboardPreread,
    DashboardUpdateProposal,
    ProviderRuntimeEvidence,
)
from .observability import DashboardWriteObservability
from .patch import compile_dashboard_patch
from .provider import EXACT_CONTRACTS, admit_provider_contract, build_provider_projection
from .raw_evidence import build_raw_dashboard_evidence
from .risk import analyze_dashboard_risk
from .semantic_diff import build_semantic_diff
from .serialization import proposal_hash, public_proposal_projection


_CANONICAL_ADDON_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_CANONICAL_CALLER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")


class ExactDashboardReader(Protocol):
    async def preread(self, *, url_path: str) -> DashboardPreread:
        """Return an internal complete raw read, never a public projection."""


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise PlanningError("Planning time must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


async def create_dashboard_update_plan(
    *,
    reader: ExactDashboardReader,
    url_path: str,
    operations: Iterable[Mapping[str, Any]],
    title: str,
    description: str,
    expiration_minutes: int,
    requested_by: str,
    authoritative_provider_slug: str,
    provider_evidence: ProviderRuntimeEvidence | None = None,
    artifact_store: DashboardArtifactStore | None = None,
    observability: DashboardWriteObservability | None = None,
    now: datetime | None = None,
    plan_id: str | None = None,
) -> DashboardUpdateProposal:
    """Build an immutable proposal and perform zero provider mutations."""

    metrics = observability or DashboardWriteObservability()
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
        raise PlanningError("Plan title is outside the reviewed bound")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS:
        raise PlanningError("Plan description is outside the reviewed bound")
    if (
        not isinstance(expiration_minutes, int)
        or isinstance(expiration_minutes, bool)
        or not MIN_EXPIRATION_MINUTES
        <= expiration_minutes
        <= MAX_EXPIRATION_MINUTES
    ):
        raise PlanningError("Plan expiration is outside the existing governance bound")
    if not isinstance(requested_by, str) or not _CANONICAL_CALLER.fullmatch(requested_by):
        raise PlanningError("Caller identity is not canonical")
    if not isinstance(authoritative_provider_slug, str) or not _CANONICAL_ADDON_SLUG.fullmatch(
        authoritative_provider_slug
    ):
        raise PlanningError("Authoritative provider slug is not canonical")
    plan_id = plan_id or secrets.token_hex(16)
    if not CANONICAL_PLAN_ID.fullmatch(plan_id):
        raise PlanningError("Plan ID is not canonical")
    created = now or datetime.now(timezone.utc)
    created_at = _utc_timestamp(created)
    expires_at = _utc_timestamp(created + timedelta(minutes=expiration_minutes))

    metrics.record("planning.preread_attempts", target=url_path)
    try:
        preread = await reader.preread(url_path=url_path)
        raw = build_raw_dashboard_evidence(preread, requested_url_path=url_path)
    except RawEvidenceError as exc:
        metrics.record(
            "planning.preread_failures",
            target=url_path,
            codes=(exc.code,),
        )
        if "storage-mode" in str(exc):
            metrics.record("planning.non_storage_rejections", target=url_path)
        raise

    try:
        compilation = compile_dashboard_patch(raw.configuration, operations)
    except (PatchValidationError, PatchCompilationError) as exc:
        metrics.record(
            "planning.patch_validation_failures", target=url_path, codes=(exc.code,)
        )
        if "16-leaf" in str(exc):
            metrics.record("planning.broad_subtree_rejections", target=url_path)
        raise
    if compilation.preread_sha256 != raw.engineering_config_sha256:
        raise PlanningError("Patch compiler preread binding drifted")
    risk = analyze_dashboard_risk(raw.configuration, compilation.resulting_configuration)
    semantic_diff = build_semantic_diff(compilation, risk)
    if risk.manual_review_required:
        metrics.record("planning.risk_review_flags", target=url_path)

    provider_evidence = provider_evidence or EXACT_CONTRACTS.get(
        raw.upstream_version
    )
    if provider_evidence is None:
        raise ProviderAdmissionError("Provider release is not reviewed")
    if provider_evidence.upstream_version != raw.upstream_version:
        raise ProviderAdmissionError("Provider release differs from preread release")
    metrics.record("provider.admission_attempts", target=url_path)
    try:
        admission = admit_provider_contract(provider_evidence)
    except ProviderAdmissionError as exc:
        metrics.record(
            "provider.admission_failures", target=url_path, codes=(exc.code,)
        )
        raise
    atomicity = assess_atomicity(raw.upstream_version)
    metrics.record(
        "atomicity.atomicity_gate_rejections",
        target=url_path,
        codes=atomicity.reason_codes,
    )
    provider_projection = build_provider_projection(
        admission=admission,
        compilation=compilation,
        url_path=url_path,
        current_config_hash=raw.upstream_config_hash,
        atomicity=atomicity,
    )

    proposal = DashboardUpdateProposal(
        model=PROPOSAL_MODEL,
        plan_id=plan_id,
        title=title.strip(),
        description=description,
        created_at=created_at,
        expires_at=expires_at,
        requested_by=requested_by,
        target_type="dashboard",
        target_id=url_path,
        raw_evidence=raw,
        compilation=compilation,
        semantic_diff=semantic_diff,
        risk=risk,
        provider_admission=admission,
        provider_projection=provider_projection,
        atomicity=atomicity,
        required_approval="external_administrator",
        rollback_available=False,
        executable=provider_projection.executable,
        lock_keys=tuple(
            sorted(
                {
                    f"dashboard:{url_path}",
                    "home_assistant:core",
                    f"addon:{authoritative_provider_slug}",
                }
            )
        ),
        proposal_sha256="",
    )
    proposal = replace(proposal, proposal_sha256=proposal_hash(proposal))
    if artifact_store is not None:
        artifact_store.create(proposal)
    metrics.record("planning.plans_created", target=url_path)
    return proposal


async def create_dashboard_update_plan_projection(**kwargs: Any) -> dict[str, Any]:
    """Bounded projection that never returns raw dashboard content."""

    proposal = await create_dashboard_update_plan(**kwargs)
    return public_proposal_projection(proposal)
