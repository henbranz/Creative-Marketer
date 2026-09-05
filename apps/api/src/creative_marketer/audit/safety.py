import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from typing import cast

from creative_marketer.audit.domain import SafeAuditMetadata

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Mapping[str, "JsonValue"] | Sequence["JsonValue"]
REDACTED = "[REDACTED]"
MAX_METADATA_BYTES = 4096
FORBIDDEN_FRAGMENTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "credential",
    "raw_prompt",
    "raw_provider_response",
    "email",
    "phone",
    "address",
    "full_name",
)
SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|\bsk-[a-z0-9_-]{8,}|\b[a-z0-9_-]{16,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
)


class AuditMetadataTooLarge(ValueError):
    pass


def _sanitize(value: JsonValue, key: str = "") -> object:
    if any(fragment in key.lower() for fragment in FORBIDDEN_FRAGMENTS):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize(child, str(child_key)) for child_key, child in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_sanitize(child) for child in value]
    if isinstance(value, str) and SENSITIVE_VALUE.search(value):
        return REDACTED
    return value


def safe_metadata(value: Mapping[str, JsonValue] | None = None) -> SafeAuditMetadata:
    serialized = json.dumps(
        _sanitize(value or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    if len(serialized.encode()) > MAX_METADATA_BYTES:
        raise AuditMetadataTooLarge("audit metadata exceeds 4096 bytes after redaction")
    return SafeAuditMetadata(serialized)


def persisted_metadata(metadata: SafeAuditMetadata) -> dict[str, object]:
    """Revalidate the safety marker at the final persistence boundary."""
    try:
        candidate = json.loads(metadata.canonical_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("safe audit metadata is not valid JSON") from error
    if not isinstance(candidate, dict):
        raise ValueError("safe audit metadata must be a JSON object")
    sanitized = safe_metadata(cast(Mapping[str, JsonValue], candidate))
    return cast(dict[str, object], json.loads(sanitized.canonical_json))


def canonical_digest(value: JsonValue) -> str:
    canonical = json.dumps(
        _sanitize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def principal_fingerprint(key: bytes, issuer: str, subject: str) -> str:
    material = json.dumps([issuer, subject], separators=(",", ":")).encode()
    return "hmac-sha256:" + hmac.new(key, material, hashlib.sha256).hexdigest()
