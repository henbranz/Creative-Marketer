import json
from uuid import uuid4

import pytest

from creative_marketer.audit.domain import (
    AuditActorKind,
    AuditOutcome,
    AuditRecord,
    AuditScopeKind,
    SafeAuditMetadata,
)
from creative_marketer.audit.safety import (
    REDACTED,
    AuditMetadataTooLarge,
    canonical_digest,
    persisted_metadata,
    principal_fingerprint,
    safe_metadata,
)


def test_nested_secrets_and_pii_are_redacted_but_safe_values_remain() -> None:
    metadata = safe_metadata(
        {
            "provider": "example",
            "Authorization": "Bearer SECRET",
            "nested": {
                "ACCESS_TOKEN": "abc",
                "password": "hunter2",
                "cookie": "session=secret",
                "api_key": "key-value",
                "refresh_token": "refresh-value",
                "provider_credential": "credential-value",
                "email": "person@example.test",
                "safe_field": "ok",
            },
            "unlabeled": "Bearer obvious-secret",
        }
    )
    value = json.loads(metadata.canonical_json)
    assert value["provider"] == "example"
    assert value["nested"]["safe_field"] == "ok"
    assert value["Authorization"] == REDACTED
    assert value["nested"]["ACCESS_TOKEN"] == REDACTED
    assert value["unlabeled"] == REDACTED
    serialized = metadata.canonical_json
    for secret in ("SECRET", "abc", "hunter2", "key-value", "refresh-value", "person@example"):
        assert secret not in serialized


def test_metadata_limit_hashing_and_fingerprint_are_deterministic() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})
    assert canonical_digest({"a": 1}) != canonical_digest({"a": 2})
    assert principal_fingerprint(b"key", "issuer", "subject") == principal_fingerprint(
        b"key", "issuer", "subject"
    )
    assert "subject" not in principal_fingerprint(b"key", "issuer", "subject")
    with pytest.raises(AuditMetadataTooLarge):
        safe_metadata({"safe": "x" * 5000})
    assert persisted_metadata(SafeAuditMetadata('{"password":"bypass"}')) == {"password": REDACTED}
    with pytest.raises(ValueError, match="JSON object"):
        persisted_metadata(SafeAuditMetadata('["not", "metadata"]'))


def test_scope_invariants_are_enforced_in_immutable_contract() -> None:
    with pytest.raises(ValueError):
        AuditRecord(
            scope_kind=AuditScopeKind.PLATFORM,
            tenant_id=uuid4(),
            actor_kind=AuditActorKind.SYSTEM,
            actor_id="system",
            action="test.action",
            outcome=AuditOutcome.SUCCESS,
            correlation_id=uuid4(),
            environment="test",
        )
    with pytest.raises(ValueError):
        AuditRecord(
            scope_kind=AuditScopeKind.TENANT,
            tenant_id=None,
            actor_kind=AuditActorKind.SYSTEM,
            actor_id="system",
            action="test.action",
            outcome=AuditOutcome.SUCCESS,
            correlation_id=uuid4(),
            environment="test",
        )
