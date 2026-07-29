"""Strict, side-effect-free models for local knowledge provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from .versioning import Applicability, IntegrationScope, VersionScope


SOURCE_ID_PATTERN = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    re.ASCII,
)
SOURCE_TYPE_PATTERN = re.compile(
    r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    re.ASCII,
)


class KnowledgeValidationError(ValueError):
    """A stable fail-closed validation outcome for local knowledge data."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class TrustClass(str, Enum):
    OFFICIAL_HOME_ASSISTANT = "official_home_assistant"
    REVIEWED_PROJECT_DOCUMENTATION = "reviewed_project_documentation"
    REVIEWED_INTEGRATION_DOCUMENTATION = "reviewed_integration_documentation"
    OPERATOR_SUPPLIED = "operator_supplied"
    UNTRUSTED_REFERENCE = "untrusted_reference"


class ContentClass(str, Enum):
    DOCUMENTATION = "documentation"
    ADR = "adr"
    POLICY = "policy"
    TROUBLESHOOTING = "troubleshooting"
    KNOWN_LIMITATION = "known_limitation"
    RELEASE_NOTE = "release_note"
    DEVICE_REFERENCE = "device_reference"


class RedactionClass(str, Enum):
    PUBLIC = "public"
    PROJECT_INTERNAL = "project_internal"
    OPERATOR_SENSITIVE = "operator_sensitive"
    RESTRICTED = "restricted"


class RetrievedTextRole(str, Enum):
    """How consumers must treat retrieved source text."""

    DATA = "data"


@dataclass(frozen=True)
class RetrievedKnowledgeText:
    """Exact UTF-8 text that remains inert, non-authoritative data."""

    source_id: str
    text: str
    content_sha256: str
    relative_path: str
    citation_prefix: str
    role: RetrievedTextRole = field(
        default=RetrievedTextRole.DATA,
        init=False,
    )
    instructions_are_authoritative: bool = field(default=False, init=False)
    instructions_executed: bool = field(default=False, init=False)


@dataclass(frozen=True)
class KnowledgeSource:
    """Validated provenance metadata for one local text source."""

    source_id: str
    source_type: str
    title: str
    publisher: str
    canonical_origin: str
    version_scope: VersionScope
    home_assistant_version_scope: VersionScope
    integration_scope: IntegrationScope
    retrieved_at: str
    valid_until: str | None
    trust_class: TrustClass
    content_class: ContentClass
    redaction_class: RedactionClass
    license_or_usage_note: str
    content_sha256: str
    relative_path: str
    citation_prefix: str

    def engineering_applicability(
        self,
        version: str | None,
    ) -> Applicability:
        return self.version_scope.evaluate(version)

    def home_assistant_applicability(
        self,
        version: str | None,
    ) -> Applicability:
        return self.home_assistant_version_scope.evaluate(version)

    def integration_applicability(
        self,
        integration_id: str | None,
        version: str | None,
    ) -> Applicability:
        return self.integration_scope.evaluate(integration_id, version)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "title": self.title,
            "publisher": self.publisher,
            "canonical_origin": self.canonical_origin,
            "version_scope": self.version_scope.canonical_dict(),
            "home_assistant_version_scope": (
                self.home_assistant_version_scope.canonical_dict()
            ),
            "integration_scope": self.integration_scope.canonical_dict(),
            "retrieved_at": self.retrieved_at,
            "valid_until": self.valid_until,
            "trust_class": self.trust_class.value,
            "content_class": self.content_class.value,
            "redaction_class": self.redaction_class.value,
            "license_or_usage_note": self.license_or_usage_note,
            "content_sha256": self.content_sha256,
            "relative_path": self.relative_path,
            "citation_prefix": self.citation_prefix,
        }


@dataclass(frozen=True)
class LoadedKnowledgeSource:
    """Validated metadata paired with exact inert source data."""

    source: KnowledgeSource
    retrieved_text: RetrievedKnowledgeText


@dataclass(frozen=True)
class KnowledgeManifest:
    """Deterministically ordered, fully validated local knowledge manifest."""

    schema_version: int
    sources: tuple[LoadedKnowledgeSource, ...]
    manifest_sha256: str
