"""Facilitator service for bounded single-automation reliability analysis."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
from datetime import datetime, timezone
import json
import re
import time

from ..errors import AutomationNotFoundError, ErrorCode, GovernanceError, InvalidRequestError
from ..facilitation import DetailLevel
from ..observability import METRICS
from ..providers import EvidenceRequest, ProviderCapability, ProviderCompleteness, ProviderFailureCategory
from .models import ReliabilityAnalysisOutput, ReliabilityEvidenceBundle
from .rules import evaluate_rules


AUTOMATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_FINDINGS = 100


class AutomationReliabilityAnalysisService:
    def __init__(self, provider, *, timeout_seconds: float = 60.0):
        self.provider = provider
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))

    async def analyze(
        self,
        *,
        automation_id: str,
        lookback_hours: int = 168,
        trace_limit: int = 10,
        detail_level: str = "standard",
        limit: int = 20,
        cursor: str = "",
    ) -> ReliabilityAnalysisOutput:
        started = time.perf_counter()
        METRICS.record_reliability_analysis_request()
        normalized_id = str(automation_id).strip()
        if not AUTOMATION_ID.fullmatch(normalized_id) or "." in normalized_id and normalized_id.startswith("automation."):
            METRICS.record_reliability_analysis_failure("request_validation")
            raise InvalidRequestError(details={"operation": "automation_reliability_analysis"})
        if detail_level not in {item.value for item in DetailLevel}:
            METRICS.record_reliability_analysis_failure("request_validation")
            raise InvalidRequestError(details={"operation": "automation_reliability_analysis"})
        if not 1 <= int(lookback_hours) <= 720 or not 1 <= int(trace_limit) <= 50 or not 1 <= int(limit) <= MAX_FINDINGS:
            METRICS.record_reliability_analysis_failure("request_validation")
            raise InvalidRequestError(details={"operation": "automation_reliability_analysis"})

        try:
            result = await asyncio.wait_for(
                self.provider.fetch(
                    EvidenceRequest(
                        capability=ProviderCapability.RELIABILITY_ANALYSIS,
                        query={
                            "automation_id": normalized_id,
                            "lookback_hours": int(lookback_hours),
                            "trace_limit": int(trace_limit),
                        },
                        max_evidence=MAX_FINDINGS,
                        detail_level=DetailLevel(detail_level),
                    )
                ),
                timeout=self.timeout_seconds,
            )
        except AutomationNotFoundError:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_reliability_analysis_failure("automation_not_found")
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            METRICS.record_provider_result("engineering", "failed")
            METRICS.record_reliability_analysis_failure("provider_timeout")
            raise GovernanceError(ErrorCode.PROVIDER_TIMEOUT) from exc

        METRICS.record_provider_result(result.provider_id, result.completeness.value)
        if not result.succeeded or not isinstance(result.data, ReliabilityEvidenceBundle):
            category = result.failure.category.value if result.failure else "provider_error"
            METRICS.record_reliability_analysis_failure(category)
            code = ErrorCode.PROVIDER_TIMEOUT if result.failure and result.failure.category == ProviderFailureCategory.TIMEOUT else ErrorCode.ANALYSIS_UNAVAILABLE
            raise GovernanceError(code)

        bundle = result.data
        findings = evaluate_rules(bundle)
        fingerprint = bundle.evidence_fingerprint()
        offset = _decode_cursor(cursor, fingerprint) if cursor else 0
        if offset > len(findings):
            METRICS.record_reliability_analysis_failure("invalid_cursor")
            raise GovernanceError(ErrorCode.INVALID_CURSOR)
        effective_limit = min(max(1, int(limit)), MAX_FINDINGS)
        page = findings[offset : offset + effective_limit]
        has_more = offset + len(page) < len(findings)
        next_cursor = _encode_cursor(fingerprint, offset + len(page)) if has_more else None
        partial = result.completeness == ProviderCompleteness.PARTIAL or bundle.partial or has_more
        if has_more:
            METRICS.record_reliability_truncation()

        include_evidence = detail_level != DetailLevel.SUMMARY.value
        evidence_ids = {reference for item in page for reference in item.evidence_references}
        evidence = [
            bundle.evidence[reference_id].public()
            for reference_id in sorted(evidence_ids)
            if reference_id in bundle.evidence
        ][:100]
        if detail_level == DetailLevel.SUMMARY.value:
            evidence = []
        elif detail_level == DetailLevel.STANDARD.value:
            evidence = [
                {key: value for key, value in item.items() if key in {"reference_id", "source_type", "summary", "timestamp"}}
                for item in evidence
            ]

        severity_counts = Counter(item.severity for item in findings)
        coverage = [item.public() for item in bundle.coverage]
        source_failures = sum(item.failed_items for item in bundle.coverage)
        traces_examined = next((item.items_examined for item in bundle.coverage if item.source_type == "automation_traces"), 0)
        entities_examined = next((item.items_examined for item in bundle.coverage if item.source_type == "entity_state"), 0)
        METRICS.record_reliability_analysis_terminal(
            partial=partial,
            finding_counts=severity_counts,
            traces_examined=traces_examined,
            referenced_entities_examined=entities_examined,
            source_failures=source_failures,
        )

        if partial:
            assessment = "partial_evidence"
        elif findings:
            assessment = "findings_present"
        else:
            assessment = "no_findings"
        warnings = list(dict.fromkeys(result.warnings + [
            warning for item in bundle.coverage for warning in item.warnings
        ]))[:20]
        if has_more:
            warnings.append("Findings were paginated; use next_cursor for the next bounded page.")
        data = {
            "target": {
                "automation_id": normalized_id,
                "entity_id": bundle.automation.get("entity_id"),
                "friendly_name": bundle.automation.get("friendly_name"),
                "enabled": str(bundle.automation.get("state", "")).lower() != "off",
                "last_triggered": bundle.automation.get("last_triggered"),
            },
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "requested_lookback_hours": int(lookback_hours),
            "trace_limit": int(trace_limit),
            "detail_level": detail_level,
            "overall_assessment": assessment,
            "result_status": "partial" if partial else "success",
            "finding_counts_by_severity": {severity: severity_counts.get(severity, 0) for severity in ("info", "low", "medium", "high", "critical")},
            "findings": [item.public(include_evidence=include_evidence) for item in page],
            "evidence_references": evidence,
            "configuration_fingerprint": bundle.configuration_fingerprint,
            "evidence_fingerprint": fingerprint,
            "evidence_source_coverage": coverage,
            "trace_coverage": next((item for item in coverage if item["source_type"] == "automation_traces"), {}),
            "entity_reference_coverage": next((item for item in coverage if item["source_type"] == "entity_state"), {}),
            "system_log_coverage": next((item for item in coverage if item["source_type"] == "system_log"), {}),
            "analysis_limitations": warnings,
            "pagination": {
                "requested_limit": int(limit),
                "effective_limit": effective_limit,
                "maximum_limit": MAX_FINDINGS,
                "returned": len(page),
                "total": len(findings),
                "has_more": has_more,
                "next_cursor": next_cursor,
                "clamped": int(limit) != effective_limit,
                "clamp_reason": "maximum_limit" if int(limit) > MAX_FINDINGS else None,
            },
            "analysis_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        metadata = {
            "routing": {
                "lifecycle_status": "beta_native",
                "classification": "engineering_native",
                "provider": "engineering",
                "fallback_occurred": False,
                "direct_access_policy": {
                    "policy_id": "single_automation_reliability_read",
                    "access": "read",
                    "scope": "one automation and bounded supporting evidence",
                },
            },
            "source_coverage": coverage,
        }
        return ReliabilityAnalysisOutput(data=data, warnings=warnings, metadata=metadata, partial=partial)


def _encode_cursor(fingerprint: str, offset: int) -> str:
    raw = json.dumps({"fingerprint": fingerprint, "offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, fingerprint: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        offset = int(value["offset"])
        cursor_fingerprint = str(value["fingerprint"])
        if offset < 0:
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GovernanceError(ErrorCode.INVALID_CURSOR) from exc
    if cursor_fingerprint != fingerprint:
        raise GovernanceError(ErrorCode.STALE_CURSOR)
    return offset

