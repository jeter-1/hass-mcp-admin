"""Local knowledge provenance contracts with no runtime integration."""

from .manifest import (
    ALLOWED_TEXT_SUFFIXES,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    MAX_KNOWLEDGE_FILE_BYTES,
    load_knowledge_manifest,
)
from .models import (
    ContentClass,
    KnowledgeManifest,
    KnowledgeSource,
    KnowledgeValidationError,
    LoadedKnowledgeSource,
    RedactionClass,
    RetrievedKnowledgeText,
    RetrievedTextRole,
    TrustClass,
)
from .versioning import (
    Applicability,
    IntegrationScope,
    IntegrationScopeKind,
    VersionScope,
    VersionScopeError,
    VersionScopeKind,
    compare_versions,
)

__all__ = [
    "ALLOWED_TEXT_SUFFIXES",
    "Applicability",
    "ContentClass",
    "IntegrationScope",
    "IntegrationScopeKind",
    "KnowledgeManifest",
    "KnowledgeSource",
    "KnowledgeValidationError",
    "LoadedKnowledgeSource",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_KNOWLEDGE_FILE_BYTES",
    "RedactionClass",
    "RetrievedKnowledgeText",
    "RetrievedTextRole",
    "TrustClass",
    "VersionScope",
    "VersionScopeError",
    "VersionScopeKind",
    "compare_versions",
    "load_knowledge_manifest",
]
