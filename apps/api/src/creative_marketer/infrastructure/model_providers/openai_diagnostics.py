"""Extract only selected scalar fields, never messages, bodies or arbitrary headers."""

from collections.abc import Mapping
from typing import cast

from openai import APIStatusError

from creative_marketer.agent_runtime.provider_diagnostics import ProviderRejection


def provider_rejection(error: APIStatusError) -> ProviderRejection:
    body = error.body
    if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping):
        body = body["error"]
    fields = body if isinstance(body, Mapping) else {}
    # The immutable value sanitizes again at construction; casts never bypass validation.
    return ProviderRejection(
        http_status=error.status_code,
        error_type=cast(str | None, fields.get("type")),
        error_code=cast(str | None, fields.get("code")),
        param=cast(str | None, fields.get("param")),
        request_id=error.request_id,
        exception_class=type(error).__name__,
    )


def safe_status_diagnostic(error: APIStatusError) -> dict[str, int | str | None]:
    return provider_rejection(error).as_dict()
