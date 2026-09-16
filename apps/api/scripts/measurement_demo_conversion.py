"""Create one local exact-reference fake conversion through the authenticated API."""

import argparse
import json
import os
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4


def _post(
    base_url: str, path: str, tenant_id: str, credential: str, body: dict[str, object]
) -> dict[str, object]:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {credential}",
            "X-Tenant-ID": tenant_id,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.loads(response.read(1024 * 1024))
    if not isinstance(value, dict):
        raise ValueError("API returned a non-object response")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a local fake attributed conversion")
    parser.add_argument("publication_id")
    parser.add_argument("--destination", default="https://example.invalid/product")
    parser.add_argument("--amount", type=Decimal, default=Decimal("49.00"))
    parser.add_argument("--currency", default="USD")
    arguments = parser.parse_args()
    base_url = os.environ.get("CM_API_BASE_URL", "http://localhost:8000")
    tenant_id = os.environ.get("CM_TENANT_ID", "").strip()
    credential = os.environ.get("CM_API_TOKEN", "").strip()
    if not tenant_id or not credential:
        raise SystemExit("CM_TENANT_ID and CM_API_TOKEN are required")
    reference = _post(
        base_url,
        f"/v1/publications/{arguments.publication_id}/attribution-references",
        tenant_id,
        credential,
        {"destination_url": arguments.destination},
    )
    conversion = _post(
        base_url,
        "/v1/measurement/dev/fake-conversions",
        tenant_id,
        credential,
        {
            "external_id": f"demo-{uuid4().hex}",
            "amount": str(arguments.amount),
            "currency": arguments.currency,
            "observed_at": datetime.now(UTC).isoformat(),
            "attribution_code": reference["public_code"],
        },
    )
    print(
        json.dumps(
            {
                "conversion_observation_id": conversion["conversion_observation_id"],
                "publication_id": conversion["publication_id"],
                "method": conversion["method"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
